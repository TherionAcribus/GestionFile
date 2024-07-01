#!/bin/bash
set -e

# Attendre que RabbitMQ soit prêt
(while true; do
  rabbitmqctl status
  if [ $? -eq 0 ]; then
    echo "RabbitMQ is up and running"
    break
  fi
  sleep 5
done) &

exec docker-entrypoint.sh rabbitmq-server