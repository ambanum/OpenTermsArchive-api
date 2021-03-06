#!/bin/bash
file_url=$(curl --silent "https://api.github.com/repos/ambanum/OpenTermsArchive-versions/releases/latest" | grep '"browser_download_url":' | sed -E 's/.*"([^"]+)".*/\1/')
echo "updating" > latest_dataset.txt
curl -LJS $file_url -o dataset.zip
unzip -o dataset.zip && rm -rf dataset && mv data/dataset-* dataset
echo $file_url > latest_dataset.txt
