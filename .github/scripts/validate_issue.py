#!/usr/bin/env python3
"""Validate community job-submission issues (US locations only)."""

import os
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import parse_issue_body, validate_location  # noqa: E402

REQUIRED_FIELDS = [
    'Company Name',
    'Role / Job Title',
    'Listing Type',
    'Location',
    'Direct Application Link',
]


def post_comment(token, repo, issue_number, body):
    requests.post(
        f'https://api.github.com/repos/{repo}/issues/{issue_number}/comments',
        headers={
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json',
        },
        json={'body': body},
        timeout=10,
    )


def main():
    token = os.environ.get('GITHUB_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    issue_number = os.environ.get('ISSUE_NUMBER')
    body = os.environ.get('ISSUE_BODY', '')

    if not body:
        print('No issue body — skipping validation')
        sys.exit(0)

    fields = parse_issue_body(body)
    errors = []

    for field in REQUIRED_FIELDS:
        if not fields.get(field, '').strip():
            errors.append(f'- **{field}** is missing or empty')

    apply_link = fields.get('Direct Application Link', '').strip()
    if apply_link and not re.match(r'^https?://\S+$', apply_link):
        errors.append('- **Direct Application Link** must be a single-line URL '
                      'starting with `http://` or `https://`')

    location = fields.get('Location', '').strip()
    if location:
        for e in validate_location(location):
            errors.append(f'- **Location**: {e}')

    if errors:
        comment = (
            'Thanks for the submission! A few things need to be fixed '
            'before this can be approved:\n\n'
            + '\n'.join(errors)
            + '\n\nPlease edit the issue to correct these and it will be reviewed.'
        )
        post_comment(token, repo, issue_number, comment)
        print(f'Validation failed: {len(errors)} error(s)')
    else:
        print('Validation passed')


if __name__ == '__main__':
    main()
