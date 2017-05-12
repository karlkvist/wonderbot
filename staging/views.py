import json

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from staging.github import github_commit_status, github_pending
from staging.models import Environment, AllowedRepository
from staging.utils import get_branch_name_from_ref


def index(request):
    context = {"environments": Environment.objects.all(),
               "repositories": AllowedRepository.objects.all()}
    return render(request, "index.html", context)


@csrf_exempt
def github_hook(request):
    """
    Handles all GitHub requests.
    """

    if request.method != "POST":
        return HttpResponse("Ignoring non-POST requests.")

    # Security signature
    # hub_signature = request.META["HTTP_X_HUB_SIGNATURE"]
    delivery = request.META["HTTP_X_GITHUB_DELIVERY"]

    # TODO check security!
    # See https://developer.github.com/webhooks/securing/

    event = request.META["HTTP_X_GITHUB_EVENT"]
    data = json.loads(request.body.decode('utf-8'))

    if event == "pull_request":

        action = data["action"]
        number = data["number"]
        repo = data["pull_request"]["head"]["repo"]["ssh_url"]
        branch = get_branch_name_from_ref(data["pull_request"]["head"]["ref"])
        sha = data["pull_request"]["head"]["sha"]

        if not AllowedRepository.objects.filter(url=repo).exists():
            return HttpResponse("Repository %s is not allowed. Ignored." % (repo,))

        if action in ["opened", "reopened"]:
            return _do_opened_pull_request(number, repo, branch, sha)

        elif action == "closed":
            return _do_closed_pull_request(number)

        else:
            return HttpResponse("%s action on PR %d ignored." % (action, number),
                                content_type="text/plain")

    elif event == "push":

        repo = data["repository"]["ssh_url"]
        branch = get_branch_name_from_ref(data["ref"])
        sha = data["after"]

        return _do_push(repo, branch, sha)

    else:
        return HttpResponse("%s event ignored." % event,
                            content_type="text/plain")


def _do_opened_pull_request(number, repo, branch, sha):
    """
    React to a new pull request being opened.
    """

    name = _environment_name_for_pr(number)
    github_pending(sha)
    environment = Environment(name=name, status=Environment.CREATING,
                              repository=repo, branch=branch, sha=sha)
    environment.save()
    environment.queue_for_creation()
    return HttpResponse("OK", content_type="text/plain")


def _do_closed_pull_request(number):
    """
    React to a pull request being closed.
    """
    name = _environment_name_for_pr(number)
    environment = Environment.objects.get(name=name)
    environment.queue_for_deletion()
    return HttpResponse("OK", content_type="text/plain")


def _do_push(repo, branch, sha):
    """
    React to commits being pushed to a branch.
    """
    environments = Environment.objects.filter(repository=repo, branch=branch)
    if not environments.exists():
        return HttpResponse("Ignoring, no environment found for repo %s and branch %s." % (repo, branch),
                            content_type="text/plain")

    github_pending(sha)
    for environment in environments:
        environment.sha = sha
        environment.save()
        environment.queue_for_update()

    return HttpResponse("OK", content_type="text/plain")


def _environment_name_for_pr(number):
    """
    Get an environment name for a pull request given its number.
    :param number: The PR number.
    :return: The name string.
    """
    return "pr-%d" % number
