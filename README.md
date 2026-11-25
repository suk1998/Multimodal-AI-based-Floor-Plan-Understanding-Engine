# FPUE — Floor Plan Understanding Engine

**Multimodal AI-based Architectural Floor Plan Understanding and Change Detection**

## Overview

FPUE is a research project for understanding architectural floor plans and detecting structural changes between reference and target drawings.

Unlike conventional pixel-based difference detection, FPUE combines computer vision, OCR, architectural geometry, spatial reasoning, and ontology-based knowledge representation.

## Research Objective

The central research hypothesis is:

If an AI system can understand a reference floor plan as a structured architectural model, it can identify changes in a target drawing through geometric and semantic correspondence.

## Understanding Pipeline

1. PDF Parsing and Rendering
2. Architectural Observation
3. OCR-based Space and Symbol Recognition
4. Architectural Geometry Analysis
5. Space Closure and Boundary Analysis
6. Spatial Relationship Grounding
7. Reference Understanding
8. Target Understanding
9. Structural Alignment and Correspondence
10. Change Detection
11. Evidence-based Change Review

## Core Technologies

- Python, FastAPI
- React, TypeScript
- OpenCV and Geometric Analysis
- PaddleOCR
- Architectural Ontology
- Spatial Grounding
- Graph-based Knowledge Representation
- Structural and Geometric Correspondence

## Architecture

- backend/: Floor plan analysis, spatial understanding, and change detection
- frontend/: Web interface
- integrations/fpue_ontology/: Architectural ontology
- poc/: Geometry processing and OCR components
- scripts/: Execution scripts

## Research Approach

FPUE follows a Reference-First approach:

**Reference Understanding → Target Understanding → Correspondence → Change Detection**

The objective is to detect architectural changes through structured understanding rather than image differencing alone.

## Project Status

Research and development.

The repository contains selected source code from the FPUE implementation.

Original architectural drawings, trained model weights, ground-truth datasets, virtual environments, and internal experimental outputs are excluded.

OCR models and runtime dependencies require separate installation. VLM inference is not enabled in the current public server workflow.
