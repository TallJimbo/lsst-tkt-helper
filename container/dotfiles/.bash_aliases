# -*- sh -*-

shopt -s direxpand

if [ -f "$HOME/.bash-git-prompt/gitprompt.sh" ]; then
    GIT_PROMPT_ONLY_IN_REPO=1
    GIT_PROMPT_FETCH_REMOTE_STATUS=0
    GIT_PROMPT_WITH_VIRTUAL_ENV=0
    GIT_PROMPT_START="(sandbox)"
    source ${HOME}/.bash-git-prompt/gitprompt.sh
fi
