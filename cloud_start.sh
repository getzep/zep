#!/usr/bin/env sh

# This script is used to start Zep in a Render Cloud environment.

# Check if ZEP_NLP_SERVER_HOSTPORT is set and is non-empty
if [ -z "${ZEP_NLP_SERVER_HOSTPORT}" ]; then
    echo "Environment variable ZEP_NLP_SERVER_HOSTPORT is not set. If running on Render.com, please ensure you are passing the NLP server's hostport as an environment variable."
else
    export ZEP_NLP_SERVER_URL="http://${ZEP_NLP_SERVER_HOSTPORT}"
    echo "ZEP_NLP_SERVER_URL has been set as ${ZEP_NLP_SERVER_URL}"
fi

/app/zep --config /app/config.yaml