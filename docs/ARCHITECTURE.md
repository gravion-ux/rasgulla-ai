# Architecture

Static pipeline: file discovery -> bounded text reads -> rule evaluation ->
structured findings -> JSON export. Future model providers must receive only
user-approved source excerpts and must not execute tools implicitly.

Planned interfaces: Analyzer, FindingStore, ModelProvider, TestPlanGenerator.
