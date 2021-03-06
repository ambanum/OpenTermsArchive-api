# pylint: disable=unused-argument
import logging
from pathlib import Path
import os
import subprocess

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import requests
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import CGUS_DATASET_PATH, RATE_LIMIT, BASE_PATH, LAST_DATASET_PATH
from data_finder import CGUsDataFinder
from dataset_parser import (
    CGUsFirstOccurenceParser,
    CGUsAllOccurencesParser,
    CGUsDataset,
)
from utils import parse_user_date, parse_date_from_dataset_url

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(openapi_url=f"{BASE_PATH}/openapi.json", docs_url=f"{BASE_PATH}/docs")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("uvicorn.error")


def read_dataset():
    """
    Get the current dataset version stored in a file
    """
    dataset_file = Path(LAST_DATASET_PATH)
    return dataset_file.read_text().strip("\n")


@app.on_event("startup")
async def startup_event():
    """
    Log current commit on startup.
    """
    logger.info(f"Built using commit {os.getenv('COMMIT_SHA', 'unknown')}")
    logger.info(f"Dataset version : {read_dataset()}")


@app.get(f"{BASE_PATH}/")
@limiter.limit(RATE_LIMIT)
async def index(request: Request):
    """
    redirect index to documentation
    """
    return RedirectResponse(f"{BASE_PATH}/docs")


@app.get(f"{BASE_PATH}/check_for_dataset")
@limiter.limit(RATE_LIMIT)
async def check_for_dataset(request: Request):
    """
    Checks if a new dataset is available,
    and downloads it
    """
    req = requests.get(
        "https://api.github.com/repos/ambanum/OpenTermsArchive-versions/releases/latest"
    )
    newest_dataset = req.json()["assets"][0]["browser_download_url"]
    current_dataset = read_dataset()

    if current_dataset == "updating":
        return {
            "status": "a new dataset is being downloaded. please wait a few minutes.",
            "most_recent_dataset": f"{newest_dataset}",
        }

    if newest_dataset != current_dataset:
        process = subprocess.Popen(["/download_dataset.sh"])
        logger.info(f"Downloading new dataset. Process pid: {process.pid}")
        return {
            "status": "a new dataset is available. downloading.",
            "most_recent_dataset": f"{newest_dataset}",
        }

    return {
        "status": "no new dataset to download",
        "most_recent_dataset": f"{newest_dataset}",
    }


@app.get(f"{BASE_PATH}/version")
@limiter.limit(RATE_LIMIT)
async def version(request: Request):
    """
    Return the current dataset version used by the API
    The list of dataset releases is available at
     https://github.com/ambanum/OpenTermsArchive-versions/releases
    Also return the commit SHA on which the API was built
    """
    dataset_url = read_dataset()
    return {
        "dataset_url": dataset_url,
        "dataset_date": parse_date_from_dataset_url(dataset_url),
        "api_version": os.getenv("COMMIT_SHA", "unknown"),
    }


@app.get(f"{BASE_PATH}/first_occurence/v1/{{term}}")
@limiter.limit(RATE_LIMIT)
async def first_occurence(request: Request, term: str):
    """
    Returns the date of first occurence of a given term for every (Service - Document Type) pair.
    Search for multiple terms by separating them with a comma (e.g. "rgpd,trackers,cookies").
    Search is case-insensitive. `false` is returned if the term is not found.
    """
    parser = CGUsFirstOccurenceParser(Path(CGUS_DATASET_PATH), term)
    parser.run()
    return parser.to_dict()


@app.get(f"{BASE_PATH}/all_occurences/v1/{{term}}")
@limiter.limit(RATE_LIMIT)
async def all_occurence(request: Request, term: str):
    """
    Returns whether a version in the dataset contains a given term.
    Search for multiple terms by separating them with a comma (e.g. "rgpd,trackers,cookies").
    Search is case-insensitive.
    """
    parser = CGUsAllOccurencesParser(Path(CGUS_DATASET_PATH), term)
    parser.run()
    return parser.to_dict()


@app.get(f"{BASE_PATH}/list_services/v1/")
@limiter.limit(RATE_LIMIT)
async def list_services(request: Request, multiple_versions_only: bool = False):
    """
    Returns a JSON object with services as keys and a list of their available document types.
    multiple_versions_only: filters out service-document pairs for which only 1 version is recorded
    """
    dataset = CGUsDataset(Path(CGUS_DATASET_PATH))
    return dataset.list_all_services_doc_types(
        multiple_versions_only=multiple_versions_only
    )


@app.get(f"{BASE_PATH}/get_version_at_date/v1/{{service}}/{{document_type}}/{{date}}")
@limiter.limit(RATE_LIMIT)
async def get_version_at_date(
    request: Request, service: str, document_type: str, date: str
):
    """
    Returns a the version for a given service and a given document type as it was on a certain date.

    The expected date format is YYYY-MM-DD.

    Example :
    /get_version_at_date/v1/Facebook/Terms of Service/2020-08-13

    {
        "service": "Facebook",
        "doc_type": "Terms of Service",
        "date": "2010-08-13T00:00:00",
        "version_at_date": "2020-08-12T14:30:11"
        "data": "Terms of Service. Welcome to Facebook! For messaging, voice and video, ..."
        "next_version": "2020-09-03T12:30:05"
    }

    """
    try:
        finder = CGUsDataFinder(service, document_type)
    except Exception as exception:
        raise HTTPException(400, str(exception)) from exception
    try:
        parsed_date = parse_user_date(date)
    except ValueError as exception:
        raise HTTPException(
            400,
            f"Issue parsing date : {str(exception)}. Expected format is YYYY-MM-DD.",
        ) from exception
    return finder.get_version_at_date(parsed_date)
