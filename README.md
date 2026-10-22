# Incident RCA Skill

## Problem

Incident root cause analysis can be daunting for newer engineers, having a skill they can rely on, especially in smaller orgs, can help them get started and solve problems day 1.

## How the skill works

The skill and associated MCP server provides a playbook and then associated read only data sources to pull in needed information to make a diagnosis. We also specifically focus on "I don't know" being a good answer as well. It redacts information as needed, and with the correct repos will also draft a PR.

## How an engineer would use it

An engineer would load up Cluade Code and either:

`/incident-rca` - interactive session where the agent asks questions, specifically what service and whats wrong

`/incident-rca [TEXT]` - agent gets right to work, pulling the service and issue out of the text

## What context/data it needs

* Service catalog
* Read only access to logs etc via MCP

## Key architectural decisions

* uv/python
* Read only data access via MCP
    * Associated budget for data access
* Redaction

## Limitations and failure modes

* Only has access to the data that is wired into the MCP servers, great for enforcing read-only, bad for flexibility
* Only understands the service catalog as is
* Budget limits how extensive the investigation can be

# Running locally/Example

Install uv

`uv sync`

`claude`

Make sure to turn on the local MCP

`/incident-rca [FIRING] SEV2 checkout-api: 5xx 14.8% over 10m, fired 09:51 UTC`

Claude should start investigating, running tool calls through the MCP and reasoning about what it is seeing.

Summary report should be [here](/examples/live-pool-exhaustion/incident-report.md)

# Reflection

If I was to deploy this today with a team of 20 engineers:

* Start out with populating a basic service catalog, and simple connections into data sources
* This is a nice low-risk tool which could be used alongside senior engineers as they do investigations, and could be referenced in a take it or leave it manner initially
* This data could be used to improve the tool as it moves into a more key role
* Ongoing evaluation of tradeoffs of custom MCP w/ read-only access vs more flexible tooling