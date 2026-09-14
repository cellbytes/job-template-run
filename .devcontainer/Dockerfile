FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-24.04

ARG USERNAME=vscode

RUN sudo apt update && sudo apt install --no-install-recommends -y vim

ENV EDITOR=vim
ENV GIT_EDITOR=vim

RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
    && mkdir /commandhistory \
    && touch /commandhistory/.bash_history \
    && chown -R $USERNAME /commandhistory \
    && echo "$SNIPPET" >> "/home/$USERNAME/.bashrc"

RUN curl -Lo chainsaw.tar.gz https://github.com/kyverno/chainsaw/releases/download/v0.2.15/chainsaw_linux_amd64.tar.gz \
    && tar -xzf chainsaw.tar.gz -C /usr/local/bin chainsaw \
    && chmod +x /usr/local/bin/chainsaw
