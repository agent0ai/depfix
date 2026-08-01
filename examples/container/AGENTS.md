# Container example

## Purpose

- Demonstrate a prepared Depfix deployment split across stable dependency and changing application layers.

## Ownership

- The Dockerfile, example application, and local run instructions form one deployment example.

## Local Contracts

- The image must prepare dependencies during build and run application code without unexpected resolution.
- The Dockerfile pins the documented Depfix release; update that pin with each release example refresh.

## Work Guidance

- Keep Docker layer ordering intentional and instructions short.

## Verification

- Build the Dockerfile and run the resulting image when container deployment behavior changes.

## Child DOX Index
