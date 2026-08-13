---
name: doc-expert
description: Professional technical documentation expert for creating PRDs, BRDs, HLDs, LLDs, TDDs, and technical solution documents. Trigger this skill when users request write document, write PRD, write BRD, write technical design, write HLD, write LLD, write TDD, solution review, create technical documentation, organize requirements, or draft a proposal.
allowed-tools: Read, Write, Bash
---

# Doc Expert

Professional technical documentation authoring for product requirements, business requirements, technical designs, and solution reviews.

## Overview

This skill creates well-structured, professionally formatted technical documents. It supports multiple document types with standardized templates, ensures logical rigor, and adapts content depth to the target audience.

## Supported Document Types

| Type | Purpose | Target Audience |
|------|---------|-----------------|
| PRD | Product Requirements Document | Product teams |
| BRD | Business Requirements Document | Business / Management |
| HLD | High-Level Design | Architecture team |
| LLD | Low-Level Design | Development team |
| TDD | Technical Design Document | Cross-functional teams |
| Solution Review | Alternative comparison & recommendation | Decision makers |

See [references/document-structures.md](references/document-structures.md) for detailed templates.

## Inputs

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `doc_type` | string | Yes | Document type: PRD, BRD, HLD, LLD, TDD, or solution-review |
| `topic` | string | Yes | Subject or feature to document |
| `context` | string | No | Additional context, requirements, or background info |
| `audience` | string | No | Target audience: product, business, dev, or mixed |
| `output_path` | string | No | Custom output directory or file path (defaults to current directory) |

## Outputs

| Output | Type | Description |
|--------|------|-------------|
| `document` | Markdown | Complete structured document in Markdown format |
| `structure` | list | Document outline with sections |
| `placeholders` | list | Missing information marked as `[TBD: xxx]` |
| `status` | string | "complete" or "needs_info" |
| `file_path` | string | Path to saved document file (user-specified or auto-generated) |

## Output Format

**Documents are saved as `.md` files**:

### Default Behavior (No output_path specified)
- Save to current working directory
- Auto-generated filename: `{DocType}-{Topic}-{YYYYMMDDHHMM}.md`
  - Example: `PRD-Login-Refactor-202604071430.md`

### User-Specified Output Path

| Input Format | Behavior | Example |
|--------------|----------|---------|
| Directory path | Save to directory with auto-generated filename | `./docs/` → `./docs/PRD-Login-Refactor-202604071430.md` |
| File path (with .md) | Save with exact filename | `./docs/my-prd.md` → `./docs/my-prd.md` |
| File path (no extension) | Append `.md` automatically | `./docs/design` → `./docs/design.md` |

### Output Requirements
1. **File content** - Pure Markdown, ready for immediate use
2. **No HTML wrapper** - Use native Markdown, not HTML-styled content
3. **Console output** - After saving, output a Markdown hyperlink as the final line of the response

### Filename Generation Rules
- DocType: PRD, BRD, HLD, LLD, TDD, or Solution
- Topic: Use PascalCase, limit to 3-5 words, remove articles (a, an, the)
- Date: Local date/time in YYYYMMDDHHMM format

## Language Rules

- **Default**: English output
- Switch to Chinese only when explicitly requested

## Writing Principles

| Principle | Description |
|-----------|-------------|
| Structure First | Fixed chapter skeleton before content filling |
| Logical Rigor | Clear cause-and-effect, no jumping conclusions |
| Precise Expression | Accurate terminology, avoid vague phrasing |
| Audience Awareness | Adjust depth for developers / product / management |
| Information Completeness | Ask for missing key details before writing |

## Workflow

### Step 1: Identify Document Type

Determine document type from user request:
- PRD keywords: "product requirements", "user story", "feature spec"
- BRD keywords: "business requirements", "ROI", "objectives"
- HLD keywords: "high level design", "architecture", "system design"
- LLD keywords: "low level design", "detailed design", "module design"
- TDD keywords: "technical design", "design doc"
- Solution keywords: "compare solutions", "review options", "pros and cons"

### Step 2: Gather Information

**Information Assessment:**

| Sufficient? | Action |
|-------------|--------|
| Yes | Proceed to Step 3 |
| No | List 3-5 critical missing questions, ask user |

**Required Information:**
- Document subject/topic
- Core requirements or objectives
- Target audience
- Constraints or dependencies (if any)

### Step 3: Select Template

Load appropriate structure from [references/document-structures.md](references/document-structures.md):

| Document | Sections |
|----------|----------|
| PRD | 10 sections (background, requirements, milestones, etc.) |
| BRD | 9 sections (executive summary, ROI, stakeholders, etc.) |
| HLD | 10 sections (architecture, modules, deployment, etc.) |
| LLD | 8 sections (data model, interfaces, test points, etc.) |
| Solution | 9 sections (alternatives, comparison, risks, etc.) |

### Step 4: Generate Document

**Process:**
1. Build document outline based on template
2. Fill sections with provided information
3. Use `[TBD: description]` for missing content
4. Adapt tone for target audience:
   - Product: Focus on user value and acceptance criteria
   - Business: Focus on ROI and strategic alignment
   - Dev: Focus on technical implementation and interfaces

**Formatting Rules:**
- **Avoid triple backticks (```)** in output documents - use single backticks (`) for inline code or indentation for code blocks instead
- This prevents rendering issues when the document is displayed in agent frontends

### Step 5: Save and Deliver

**Determine output path:**

| Scenario | Action |
|----------|--------|
| User specifies directory | Use: `{output_path}/{DocType}-{Topic}-{YYYYMMDDHHMM}.md` |
| User specifies file path | Use exact path (add `.md` if missing) |
| No output_path provided | Use: `./{DocType}-{Topic}-{YYYYMMDDHHMM}.md` |

**Save the document:**
1. Resolve final file path based on above rules
2. Use Write tool to save file
3. Confirm save success and output the absolute file path

**Quality Checklist:**
- [ ] All required sections present
- [ ] Logical flow between sections
- [ ] No placeholder text left for provided info
- [ ] TBD markers for genuinely missing info
- [ ] Consistent terminology throughout
- [ ] Audience-appropriate depth
- [ ] File successfully saved to disk

**Console output format:**

The final line of your response must be:

`[file-name.md](file:///fileAbsolutePath)`

Replace `file-name.md` with the actual filename. Replace `fileAbsolutePath` with the actual file absolute path. This is a Markdown hyperlink; render it as clickable text. Important: The file path must be the actual file absolute path.

Example:
`[PRD-Login-Refactor-202604071430.md](file:///workspace/PRD-Login-Refactor-202604071430.md)`

## Error Handling

| Error Scenario | Cause | Solution |
|----------------|-------|----------|
| Unclear document type | Ambiguous user request | Ask user to specify PRD/BRD/HLD/LLD/TDD/Solution |
| Insufficient context | Missing key requirements | Request specific details before proceeding |
| Unknown domain | Unfamiliar technology/concept | Use generic template, mark domain-specific sections as TBD |
| Conflicting requirements | Contradictory inputs | Highlight conflicts, ask for clarification |
| File save failed | Permission denied or path issue | Report error and output document content directly |
| Filename collision | File already exists | Append counter suffix: `-v2`, `-v3` etc. |

## Examples

### Basic Usage
- "Write a PRD for user login feature"
- "Create BRD for payment system upgrade"
- "Generate HLD for microservices migration"
- "Draft solution review comparing Redis vs Memcached"
- "Help me document the API gateway architecture"

### With Custom Output Path
- "Write a PRD for user login and save it to ./docs/"
- "Create BRD for payment system and save as ./requirements/payment-v2.md"
- "Generate HLD for microservices migration to ./design-docs/architecture.md"
