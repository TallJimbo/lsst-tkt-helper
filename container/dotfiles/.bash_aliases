# -*- sh -*-

export EDITOR="emacsclient -t"

alias ec='emacsclient -t'

if [ -f "$HOME/.bash-git-prompt/gitprompt.sh" ]; then
    GIT_PROMPT_ONLY_IN_REPO=1
    GIT_PROMPT_FETCH_REMOTE_STATUS=0
    source ${HOME}/.bash-git-prompt/gitprompt.sh
fi

shopt -s direxpand

if [ -f "/opt/lsst/software/stack/loadLSST.bash" ]; then
    source /opt/lsst/software/stack/loadLSST.bash
    if [ -f "/workspace/ups" ]; then
        setup -r /workspace
    fi
fi
