#!/usr/bin/env bash

docker build -t docker.force.fm/msa/msa_rcalendar:latest \
             -t docker.force.fm/msa/msa_rcalendar:0.0.0 \
             -t ncrawler/msa_rcalendar:latest \
             -t ncrawler/msa_rcalendar:0.0.0 \
             .
