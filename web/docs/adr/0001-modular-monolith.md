# ADR 0001: Incremental modular monolith

Status: accepted

OpenDelta already deploys one authenticated web app and one FastAPI service with shared persistent data. Microservices would add networks, credentials, queues, and partial failures before measured scale requires them.

V1 keeps one backend and introduces domain/service/repository/API boundaries under `opendelta`. Legacy engines are reached through composition-root adapters. Stable contracts permit future extraction.
