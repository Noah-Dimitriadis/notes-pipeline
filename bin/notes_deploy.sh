#!/bin/bash

# get user input for environment 
read -e -p "Environment: " environment 

if [[ $environment == "mac" ]]; then 
  ssh treehouse
  cd Documents/notes-pipeline/deploy
  docker compose build
  docker compose up -d
  echo "Docker build finished $(docker ps -a)"
  exit

elif [[$environment == "treehouse"]]; treehouse
  cd deploy/
  docker compose build
  docker compose up -d
  exit 
else
  echo "invalid"
  exit


#optional add in a health check and rollback for fully automated deployments
#this is strictly user monitored for now
