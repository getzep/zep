#!/bin/bash

# List of supported docker compose commands
DOCKER_COMPOSE_COMMANDS=("up" "pull" "down" "logs" "ps" "restart" "stop" "start")

_make() {
    make -f Makefile.ce "${@:1}"
}

CMD="${1}"

# Function to check if a value is in an array
contains_element() {
    local e match="$1"
    shift
    for e; do [[ "$e" == "$match" ]] && return 0; done
    return 1
}

# Check if the command is in the list of supported docker compose commands
if contains_element "$CMD" "${DOCKER_COMPOSE_COMMANDS[@]}"; then
    docker compose -f docker-compose.ce.yaml "$CMD" "${@:2}"
elif [ "$CMD" = "make" ]; then
    _make "${@:2}"
else
    echo "${CMD} is not a valid command"
    echo "Usage: "
    echo "     ./zep [$(printf "%s | " "${DOCKER_COMPOSE_COMMANDS[@]}" | sed 's/ | $//')]"
    echo "     ./zep make <target>"
fi
