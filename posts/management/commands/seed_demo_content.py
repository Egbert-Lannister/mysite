from __future__ import annotations

import io
from datetime import datetime, timezone as datetime_timezone
from textwrap import dedent

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image, ImageDraw, ImageFont

from posts.models import Post, ProjectMedia, Series


MEDIA_PROJECT_SLUG = "demo-media-layout-workbench"
STRUCTURED_PROJECT_FIELDS = (
    ("project_method", "Method"),
    ("project_experiments", "Experiments"),
    ("project_conclusion", "Conclusion"),
)


def _date(year: int, month: int, day: int) -> datetime:
    """Return a fixed, explicitly UTC date so repeated seeds never drift."""
    return datetime(year, month, day, 9, 0, tzinfo=datetime_timezone.utc)


def _markdown(value: str) -> str:
    return dedent(value).strip()


SERIES_SPECS = (
    {
        "title": "Demo: Reliable Engineering Practice",
        "slug": "demo-reliable-engineering-practice",
        "description": (
            "A two-part demonstration series about retries, schema changes, and the "
            "operational details that make routine engineering work easier to reason about."
        ),
        "order": 20,
        "is_featured": True,
    },
    {
        "title": "Demo: Evaluation Practice",
        "slug": "demo-evaluation-practice",
        "description": (
            "A two-part demonstration series about reproducible evaluation harnesses and "
            "thoughtful dataset slices. It describes method, not experimental findings."
        ),
        "order": 21,
        "is_featured": True,
    },
)


ARTICLE_SPECS = (
    {
        "title": "Designing Deployment Jobs That Can Safely Retry",
        "slug": "demo-idempotent-deployment-jobs",
        "description": (
            "A practical pattern for making deployment jobs resumable: stable operation keys, "
            "explicit checkpoints, and a clear boundary between completed work and remaining work."
        ),
        "category": "engineering",
        "series": "demo-reliable-engineering-practice",
        "series_order": 1,
        "date": _date(2026, 8, 14),
        "tags": ("deployment", "idempotency", "operations"),
        "content": _markdown("""
            A retry is not an exceptional path for a deployment job. A worker can stop after an
            external side effect but before recording that the step finished. If the next attempt
            blindly repeats every action, the job becomes difficult to operate precisely when an
            operator most needs a predictable recovery path.

            ## Start with a stable operation key

            Give each logical deployment an operation key derived from its purpose, such as the
            release identifier and target environment. The key should remain stable across retries,
            but it should not be reused for an unrelated release. Downstream steps can use the key
            to recognize work they have already accepted.

            A lock is useful for preventing concurrent execution, but a lock is not an idempotency
            mechanism. Once a lock expires, the system still needs a durable record of intent and
            progress. Keep those two concerns separate.

            ## Checkpoint decisions, not just activity

            A useful checkpoint records the step name, the operation key, and the information needed
            to resume. For example, after creating a release artifact, store the artifact reference
            rather than rebuilding it on the next attempt. After handing work to another service,
            store the remote operation reference so the retry can query its state.

            ```text
            begin operation
            resolve or create artifact reference
            record artifact reference
            resolve or submit remote operation
            record remote operation reference
            poll until a terminal state is observed
            mark the deployment complete
            ```

            Each transition should have one clear owner. If a process writes the checkpoint and also
            performs the remote call, define their order and document the ambiguous window. Usually
            the safe retry behavior is to reconcile with the remote system before issuing another
            call.

            ## Make failure handling observable

            Log the operation key, step, attempt, and reconciliation decision as structured fields.
            A message that merely says "retrying" hides the important question: which side effect is
            about to be repeated? Operators should be able to distinguish a skipped completed step
            from a repeated one.

            Before using the job for a real release, exercise interruption between every side effect
            and its checkpoint in a disposable environment. The expected result is not a particular
            speed improvement; it is a job that can explain its state and finish without duplicating
            logical work.
        """),
    },
    {
        "title": "A Practical Runbook for Backward-Compatible Schema Changes",
        "slug": "demo-backward-compatible-schema-changes",
        "description": (
            "An expand-migrate-contract runbook for changing a schema while old and new application "
            "versions coexist, with explicit ownership for backfills and rollback decisions."
        ),
        "category": "engineering",
        "series": "demo-reliable-engineering-practice",
        "series_order": 2,
        "date": _date(2026, 9, 4),
        "tags": ("databases", "migrations", "release practice"),
        "content": _markdown("""
            Schema changes become risky when a migration assumes that every running process already
            understands the new shape. During a rolling deployment, old and new code can share the
            same database. A useful runbook therefore starts with compatibility, not with the SQL
            statements.

            ## Expand without breaking current readers

            Add new structures in a form that existing code can ignore. A new nullable column or a
            new table is usually easier to introduce than an immediate rename. Keep the old write
            path until the new path is understood, and avoid coupling a large data rewrite to the
            schema deployment.

            Write down the compatibility promise for this stage: old code can still read and write,
            while new code may begin using the added structure. If that promise is not true, the
            change needs a different rollout plan.

            ## Migrate with resumable ownership

            A backfill should have bounded batches, a durable cursor, and a clear rule for rows that
            change while the batch runs. The cursor belongs to the backfill operation, not to an
            individual worker process, so another worker can continue after an interruption.

            Reconciliation is part of the migration rather than an optional final query. Define how
            to find rows missing new values, how to handle conflicts, and when dual writes may stop.
            Do not invent a completion criterion from row counts alone; the criterion should reflect
            the application's actual read and write behavior.

            ## Contract only after the old contract is unused

            Removing a column or constraint is a separate release decision. First confirm that old
            application versions are no longer running and that rollback will not require the removed
            structure. Then remove compatibility code and obsolete schema in small, reviewable steps.

            The runbook should name the evidence required at every gate: deployment state, backfill
            state, reconciliation findings, and a rollback decision. This example is deliberately
            neutral about a particular database product. The transferable idea is to make every
            intermediate state valid for both the data and the code that is still running.
        """),
    },
    {
        "title": "Building an Evaluation Harness You Can Reproduce",
        "slug": "demo-reproducible-evaluation-harness",
        "description": (
            "A method for keeping evaluation inputs, configuration, and outputs traceable without "
            "turning a research harness into an opaque collection of one-off scripts."
        ),
        "category": "research",
        "series": "demo-evaluation-practice",
        "series_order": 1,
        "date": _date(2026, 8, 28),
        "tags": ("evaluation", "reproducibility", "research engineering"),
        "content": _markdown("""
            An evaluation harness should answer a basic question long after the original run: what
            exactly was evaluated, with which inputs and decisions? Reproducibility begins by making
            those decisions explicit. It does not require inventing a metric or claiming that two
            environments are identical.

            ## Version the evaluation unit

            Define one evaluation unit, such as an example, a task, or a trace. Give each unit a
            stable identifier and keep its input reference, expected output representation, and any
            permitted preprocessing together. If a filter excludes an example, record that decision
            as part of the run manifest instead of silently changing the denominator.

            A manifest can include dataset revision, code revision, model or system identifier,
            decoding configuration, dependency information, and the time the run started. These are
            descriptions of provenance, not evidence that the result is correct.

            ## Separate execution from scoring

            Keep raw outputs before metric computation. A scorer can then be corrected or compared
            with another implementation without repeating the system execution. Store enough
            structure to associate every output with its evaluation unit and configuration.

            ```text
            immutable input manifest
                  -> execution records
                  -> normalized predictions
                  -> scoring tables
                  -> human-readable report
            ```

            Normalization deserves special attention. Case folding, whitespace handling, answer
            extraction, and error classification can change interpretation. Put these rules in
            reviewed code and include examples that show how each rule behaves.

            ## Report limitations alongside tables

            A report should identify missing outputs, parser failures, excluded examples, and any
            manual decisions. It should also distinguish a planned comparison from an exploratory
            inspection. Otherwise, repeated inspection can quietly turn into selective reporting.

            A reproduction check can be simple: start from the saved manifest in a clean workspace,
            rebuild the scoring tables from stored outputs, and compare their structure and values
            under the stated numerical tolerance. That check validates the scoring path only; it
            should not be described as reproducing external system behavior unless execution was
            actually repeated under the documented conditions.
        """),
    },
    {
        "title": "Dataset Slices Are Questions, Not Just Labels",
        "slug": "demo-dataset-slices-as-questions",
        "description": (
            "A research note on defining dataset slices around interpretable questions, preserving "
            "denominators, and avoiding conclusions that a small or ambiguous slice cannot support."
        ),
        "category": "research",
        "series": "demo-evaluation-practice",
        "series_order": 2,
        "date": _date(2026, 9, 25),
        "tags": ("datasets", "evaluation", "methodology"),
        "content": _markdown("""
            A dataset slice is often presented as a column in a results table, but its real content
            is the question used to construct it. "Long inputs," "difficult examples," and "unusual
            cases" are not reproducible categories until the inclusion rule is written down.

            ## Define the rule before interpreting the outcome

            Start with a sentence that explains what the slice is intended to isolate. Then translate
            that sentence into a mechanical rule using observable fields. If human judgment is part
            of the rule, preserve reviewer instructions and disagreement handling. A label by itself
            does not provide that information.

            Where possible, define the rule before looking at model outcomes. Exploratory slices are
            legitimate, but they should be labeled as exploratory so a reader does not mistake them
            for a preregistered comparison.

            ## Keep the denominator visible

            Every sliced result needs a denominator and an account of missing or excluded examples.
            A filtered table can otherwise make unlike populations look comparable. Keep the total
            number of eligible examples, the number included in the slice, and the number actually
            scored in the same report.

            Small or overlapping slices also require careful language. A descriptive observation can
            motivate a follow-up question without establishing a general effect. This article gives
            no empirical result; its purpose is to make that distinction easier to preserve.

            ## Check what the slice correlates with

            A slice intended to measure one property may also select for another. Input length can be
            related to formatting, source, or task family. Rather than treating the slice name as a
            causal explanation, inspect composition and state plausible alternatives.

            Useful slice documentation includes the inclusion rule, source fields, creation stage,
            overlap with other slices, and known limitations. With that context, a reader can decide
            whether the proposed interpretation follows from the evidence or needs another controlled
            comparison.
        """),
    },
    {
        "title": "Debugging Notes: Keep a Hypothesis Ledger",
        "slug": "demo-debugging-hypothesis-ledger",
        "description": (
            "A compact debugging practice for separating observations from guesses, choosing the "
            "next discriminating check, and preserving a useful trail after the issue is resolved."
        ),
        "category": "notes",
        "series": None,
        "series_order": None,
        "date": _date(2026, 10, 9),
        "tags": ("debugging", "notes", "problem solving"),
        "content": _markdown("""
            During a confusing debugging session, observations and interpretations quickly become
            tangled. A hypothesis ledger is a lightweight way to keep them separate. It is a working
            note, not a formal report, and it should make the next check easier to choose.

            ## Record observations without a theory

            Write what was directly observed: an error message, an unexpected state, a reproducible
            input, or a boundary where behavior changes. Include the environment and the exact check
            used. Avoid phrases such as "the cache is broken" until that statement has been tested.

            A short observation table is enough:

            | Check | Observation | Interpretation |
            | --- | --- | --- |
            | Repeat with the same input | Same boundary appears | Reproduction is stable under these conditions |
            | Remove an optional layer | Boundary remains | That layer is not required for reproduction |

            The third column is deliberately separate. It records a conclusion that may later need
            revision.

            ## Rank hypotheses by the next useful distinction

            For each plausible explanation, ask what observation would make it more or less likely.
            Prefer a small check that distinguishes two hypotheses over a broad change that merely
            makes the original problem harder to interpret. Change one relevant condition at a time
            when the experiment allows it.

            A ledger entry can be three lines: the hypothesis, the proposed check, and the result.
            If the check is inconclusive, say so and keep the result. Inconclusive checks often reveal
            that the reproduction itself is underspecified.

            ## Close with a reproducible summary

            When the issue is understood, summarize the triggering conditions, the mechanism, the
            smallest relevant reproduction, and the reason the chosen fix addresses that mechanism.
            Also note any earlier observations that remain unexplained rather than quietly discarding
            them.

            The value of the ledger is not exhaustive documentation. It is a visible chain from
            evidence to action, which helps both the current debugging session and the next person
            who encounters a similar boundary.
        """),
    },
    {
        "title": "A Small Checklist for Design Reviews",
        "slug": "demo-design-review-checklist",
        "description": (
            "A focused checklist for reviewing a technical design through its interfaces, failure "
            "states, migration path, and operational ownership instead of its diagram alone."
        ),
        "category": "notes",
        "series": None,
        "series_order": None,
        "date": _date(2026, 10, 16),
        "tags": ("design review", "technical writing", "engineering practice"),
        "content": _markdown("""
            A design review should reduce ambiguity before implementation makes a decision expensive
            to change. The most useful checklist is therefore short enough to use and specific enough
            to expose missing decisions.

            ## Purpose and boundaries

            Can a reader state the problem without relying on the proposed solution? Check that the
            document identifies users or callers, the current constraint, and what is explicitly out
            of scope. If alternatives are listed, record why each was accepted or rejected under the
            same set of constraints.

            ## Interfaces and ownership

            Identify every boundary: public function, API, data schema, queue message, file format, or
            manual operating procedure. For each one, name the owner and the compatibility promise.
            A component diagram without these details often hides the decisions that matter most.

            - What input is valid, and how is invalid input reported?
            - Which state is authoritative, and which state can be rebuilt?
            - Who can retry an operation, and what makes a retry safe?
            - Which existing behavior must remain unchanged during migration?

            ## Failure and recovery

            Walk through a slow dependency, a malformed message, an interrupted worker, and a partial
            deployment. The goal is not to claim that every failure is handled. It is to reveal which
            failures have explicit behavior and which still need a decision.

            Check that recovery is described from the operator's perspective. Include how to inspect
            state, how to stop new work, and how to resume or roll back without losing the meaning of
            existing records.

            ## Review outcome

            End with decisions, open questions, and owners for follow-up work. Avoid converting an
            unresolved concern into vague approval. A concise review record should let a later reader
            distinguish an intentional tradeoff from an issue nobody examined.
        """),
    },
)


PROJECT_SPECS = (
    {
        "title": "Demo Media Layout Workbench",
        "slug": MEDIA_PROJECT_SLUG,
        "description": (
            "A public demonstration project containing original geometric fixtures for reviewing "
            "wide, landscape, square, portrait, and small media layouts. It contains no research data."
        ),
        "date": _date(2026, 10, 20),
        "project_status": "active",
        "project_featured": True,
        "project_tech_stack": "Python, Django, Pillow",
        "tags": ("demo", "media layout", "Pillow"),
        "overview": (
            "This demonstration project provides a deterministic set of original geometric images "
            "for reviewing aspect ratios, captions, grouping, and ownership on a project page. Every "
            "figure is an illustrative layout fixture rather than experimental evidence."
        ),
        "project_method": (
            "Pillow draws each PNG in memory with a fixed size and simple vector-like shapes. The "
            "seed command saves it through the ProjectMedia FileField so the configured storage, "
            "rather than a hard-coded path, determines its final location."
        ),
        "project_experiments": (
            "The review pass checks wide, ordinary landscape, square, portrait, and small images "
            "across method, results, comparison, and gallery sections. No measurements or model "
            "outputs are attached to these figures."
        ),
        "project_conclusion": (
            "The fixture set offers a repeatable basis for visual and editorial review without "
            "reusing production research assets or implying a performance claim."
        ),
    },
    {
        "title": "Demo Editorial Release Checklist",
        "slug": "demo-editorial-release-checklist",
        "description": (
            "A public example of documenting an editorial release process with explicit review "
            "boundaries, reversible publication steps, and no fabricated external resources."
        ),
        "date": _date(2026, 10, 22),
        "project_status": "active",
        "project_featured": False,
        "project_tech_stack": "Django, Markdown",
        "tags": ("demo", "editorial workflow", "checklist"),
        "overview": (
            "This demonstration project shows how a small editorial release checklist can be "
            "presented as a public project. It is a documentation fixture, not a hosted service, and "
            "therefore intentionally has no repository, paper, or live-demo link."
        ),
        "project_method": (
            "Checks are separated into authorship, media, and publication. Authorship covers a clear "
            "summary and meaningful body; media covers ownership, captions, and alternative text; "
            "publication covers visibility, navigation, and a reversible release decision."
        ),
        "project_experiments": (
            "A reviewer can walk through the checklist against draft content in a disposable "
            "environment and record unresolved questions. The demonstration supplies the procedure "
            "only and does not claim measured outcomes."
        ),
        "project_conclusion": (
            "The example favors explicit review evidence and honest empty links over decorative "
            "completeness. It can be repeated without altering unrelated articles or project media."
        ),
    },
)


MEDIA_SPECS = (
    {
        "filename": "illustrative-wide-method.png",
        "width": 1600,
        "height": 640,
        "pattern": "method",
        "section": "method",
        "group_name": "Demo layout fixtures",
        "order": 1,
        "title": "Illustrative Demo: Wide Method Map",
        "description": "Original wide geometric fixture for method layout review; no research data.",
    },
    {
        "filename": "illustrative-landscape-panel.png",
        "width": 1200,
        "height": 800,
        "pattern": "landscape",
        "section": "results",
        "group_name": "Demo layout fixtures",
        "order": 1,
        "title": "Illustrative Demo: Landscape Results Placeholder",
        "description": "Original landscape placeholder for results-grid review; contains no measurements.",
    },
    {
        "filename": "illustrative-square-comparison.png",
        "width": 1000,
        "height": 1000,
        "pattern": "comparison",
        "section": "comparison",
        "group_name": "Demo visual states",
        "order": 1,
        "title": "Illustrative Demo: Square Comparison",
        "description": "Original square comparison fixture with abstract states, not experimental results.",
    },
    {
        "filename": "illustrative-portrait-gallery.png",
        "width": 800,
        "height": 1200,
        "pattern": "portrait",
        "section": "gallery",
        "group_name": "Demo aspect ratios",
        "order": 1,
        "title": "Illustrative Demo: Portrait Gallery Card",
        "description": "Original portrait fixture for gallery containment and caption alignment.",
    },
    {
        "filename": "illustrative-small-probe.png",
        "width": 320,
        "height": 240,
        "pattern": "small",
        "section": "gallery",
        "group_name": "Demo aspect ratios",
        "order": 2,
        "title": "Illustrative Demo: Small Layout Probe",
        "description": "Original small PNG for testing intrinsic dimensions and compact media frames.",
    },
)


PALETTE = {
    "graphite": "#171A18",
    "green": "#63D69C",
    "paper": "#F7F8F6",
    "muted": "#CBD2CC",
    "white": "#FFFFFF",
}


def _model_has_field(model, name: str) -> bool:
    return name in {field.name for field in model._meta.get_fields()}


def _project_content(spec: dict) -> str:
    """Use structured fields when available, otherwise keep Markdown section fallbacks."""
    content = f"## Overview\n\n{spec['overview']}"
    for field_name, heading in STRUCTURED_PROJECT_FIELDS:
        if not _model_has_field(Post, field_name):
            content += f"\n\n## {heading}\n\n{spec[field_name]}"
    return content


def _font(size: int, bold: bool = False):
    candidates = ("DejaVuSans-Bold.ttf", "arialbd.ttf") if bold else ("DejaVuSans.ttf", "arial.ttf")
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered_text(draw, box, text, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = (box[0] + box[2] - width) / 2 - bounds[0]
    y = (box[1] + box[3] - height) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=fill)


def _render_demo_image(spec: dict) -> bytes:
    """Draw an original fixture entirely in memory; no source assets are read."""
    width, height = spec["width"], spec["height"]
    image = Image.new("RGB", (width, height), PALETTE["paper"])
    draw = ImageDraw.Draw(image)
    unit = min(width, height)
    margin = max(12, unit // 18)
    stroke = max(2, unit // 180)
    radius = max(6, unit // 45)
    title_font = _font(max(11, unit // 28), bold=True)
    label_font = _font(max(10, unit // 38), bold=True)
    small_font = _font(max(9, unit // 48))

    draw.rounded_rectangle(
        (margin, margin, width - margin, height - margin),
        radius=radius,
        fill=PALETTE["white"],
        outline=PALETTE["muted"],
        width=stroke,
    )
    draw.text(
        (margin * 1.45, margin * 1.25),
        "ILLUSTRATIVE DEMO",
        font=title_font,
        fill=PALETTE["graphite"],
    )
    dot_radius = max(4, unit // 80)
    dot_x = width - margin * 1.6
    dot_y = margin * 1.6
    draw.ellipse(
        (dot_x - dot_radius, dot_y - dot_radius, dot_x + dot_radius, dot_y + dot_radius),
        fill=PALETTE["green"],
    )

    top = margin * 3
    bottom = height - margin * 2.3
    left = margin * 1.6
    right = width - margin * 1.6
    pattern = spec["pattern"]

    if pattern == "method":
        labels = ("INPUT", "CHECK", "OUTPUT")
        gap = (right - left) * 0.065
        box_width = (right - left - 2 * gap) / 3
        box_top = top + (bottom - top) * 0.2
        box_bottom = bottom - (bottom - top) * 0.2
        for index, label in enumerate(labels):
            x0 = left + index * (box_width + gap)
            x1 = x0 + box_width
            fill = PALETTE["graphite"] if index == 1 else PALETTE["paper"]
            text_color = PALETTE["white"] if index == 1 else PALETTE["graphite"]
            draw.rounded_rectangle((x0, box_top, x1, box_bottom), radius=radius, fill=fill, outline=PALETTE["muted"], width=stroke)
            _centered_text(draw, (x0, box_top, x1, box_bottom), label, label_font, text_color)
            if index < 2:
                line_y = (box_top + box_bottom) / 2
                line_start = x1 + gap * 0.18
                line_end = x1 + gap * 0.82
                draw.line((line_start, line_y, line_end, line_y), fill=PALETTE["green"], width=stroke * 3)
                draw.polygon(((line_end, line_y), (line_end - gap * 0.2, line_y - gap * 0.16), (line_end - gap * 0.2, line_y + gap * 0.16)), fill=PALETTE["green"])

    elif pattern == "landscape":
        columns = ((left, left + (right - left) * 0.57), (left + (right - left) * 0.62, right))
        labels = (("MEDIA", "CAPTION"), ("GROUP", "CONTEXT"))
        row_gap = margin * 0.35
        row_height = (bottom - top - row_gap) / 2
        for column_index, (x0, x1) in enumerate(columns):
            for row_index in range(2):
                y0 = top + row_index * (row_height + row_gap)
                y1 = y0 + row_height
                is_dark = column_index == 0 and row_index == 0
                draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=PALETTE["graphite"] if is_dark else PALETTE["paper"], outline=PALETTE["muted"], width=stroke)
                _centered_text(draw, (x0, y0, x1, y1), labels[column_index][row_index], label_font, PALETTE["white"] if is_dark else PALETTE["graphite"])
                if is_dark:
                    draw.rectangle((x0 + (x1 - x0) * 0.15, y0 + (y1 - y0) * 0.23, x0 + (x1 - x0) * 0.34, y0 + (y1 - y0) * 0.42), fill=PALETTE["green"])

    elif pattern == "comparison":
        gap = margin * 0.8
        box_width = (right - left - gap) / 2
        for index, label in enumerate(("STATE A", "STATE B")):
            x0 = left + index * (box_width + gap)
            x1 = x0 + box_width
            draw.rounded_rectangle((x0, top, x1, bottom), radius=radius, fill=PALETTE["paper"], outline=PALETTE["muted"], width=stroke)
            center_x = (x0 + x1) / 2
            center_y = (top + bottom) / 2
            shape_size = (bottom - top) * 0.19
            if index == 0:
                draw.ellipse((center_x - shape_size, center_y - shape_size, center_x + shape_size, center_y + shape_size), fill=PALETTE["graphite"])
                draw.ellipse((center_x - shape_size * 0.35, center_y - shape_size * 0.35, center_x + shape_size * 0.35, center_y + shape_size * 0.35), fill=PALETTE["green"])
            else:
                draw.rounded_rectangle((center_x - shape_size, center_y - shape_size, center_x + shape_size, center_y + shape_size), radius=radius, fill=PALETTE["graphite"])
                draw.line((center_x - shape_size * 0.6, center_y + shape_size * 0.45, center_x + shape_size * 0.6, center_y - shape_size * 0.45), fill=PALETTE["green"], width=stroke * 5)
            _centered_text(draw, (x0, bottom - (bottom - top) * 0.2, x1, bottom - margin * 0.2), label, label_font, PALETTE["graphite"])

    elif pattern == "portrait":
        labels = ("FRAME", "CAPTION", "CONTEXT")
        gap = margin * 0.55
        card_height = (bottom - top - 2 * gap) / 3
        for index, label in enumerate(labels):
            y0 = top + index * (card_height + gap)
            y1 = y0 + card_height
            inset = index * margin * 0.35
            draw.rounded_rectangle((left + inset, y0, right - inset, y1), radius=radius, fill=PALETTE["graphite"] if index == 0 else PALETTE["paper"], outline=PALETTE["muted"], width=stroke)
            _centered_text(draw, (left + inset, y0, right - inset, y1), label, label_font, PALETTE["white"] if index == 0 else PALETTE["graphite"])
            if index == 0:
                draw.ellipse((right - inset - margin * 1.1, y0 + margin * 0.35, right - inset - margin * 0.55, y0 + margin * 0.9), fill=PALETTE["green"])

    else:
        box = (left, top, left + (right - left) * 0.38, bottom)
        draw.rounded_rectangle(box, radius=radius, fill=PALETTE["graphite"])
        dot = max(5, unit // 30)
        center_x = (box[0] + box[2]) / 2
        center_y = (box[1] + box[3]) / 2
        draw.ellipse((center_x - dot, center_y - dot, center_x + dot, center_y + dot), fill=PALETTE["green"])
        text_left = left + (right - left) * 0.46
        draw.text((text_left, top + (bottom - top) * 0.2), "SMALL", font=label_font, fill=PALETTE["graphite"])
        draw.text((text_left, top + (bottom - top) * 0.52), "LAYOUT", font=label_font, fill=PALETTE["graphite"])

    footer = f"{width} x {height} | ORIGINAL FIXTURE | NO RESEARCH DATA"
    draw.text((margin * 1.45, height - margin * 1.65), footer, font=small_font, fill=PALETTE["graphite"])
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


class Command(BaseCommand):
    help = (
        "Create missing, explicitly named demo content and original layout media. "
        "Existing rows are never updated or deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report missing demo objects without writing to the database or media storage.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self._dry_run()
            return

        created = {"series": 0, "posts": 0, "media": 0}
        existing = {"series": 0, "posts": 0, "media": 0}
        created_files = []
        try:
            with transaction.atomic():
                series_by_slug = self._seed_series(created, existing)
                self._seed_articles(series_by_slug, created, existing)
                self._seed_projects(created, existing)
                self._seed_media(created, existing, created_files, options["verbosity"])
        except Exception:
            # Storage is not transactional. Remove only files written by this invocation.
            for storage, name in created_files:
                storage.delete(name)
            raise

        self.stdout.write(self.style.SUCCESS(
            "Demo content complete. "
            f"Created: {created['series']} series, {created['posts']} posts, {created['media']} media. "
            f"Already present: {existing['series']} series, {existing['posts']} posts, {existing['media']} media. "
            "No existing rows were updated or deleted."
        ))

    def _report(self, label: str, slug: str, is_created: bool, created: dict, existing: dict, kind: str):
        if is_created:
            created[kind] += 1
            self.stdout.write(self.style.SUCCESS(f"Created {label}: {slug}"))
        else:
            existing[kind] += 1
            self.stdout.write(f"Kept existing {label}: {slug}")

    def _seed_series(self, created, existing):
        series_by_slug = {}
        for spec in SERIES_SPECS:
            defaults = {key: value for key, value in spec.items() if key != "slug"}
            series, is_created = Series.objects.get_or_create(slug=spec["slug"], defaults=defaults)
            series_by_slug[spec["slug"]] = series
            self._report("series", spec["slug"], is_created, created, existing, "series")
        return series_by_slug

    def _seed_articles(self, series_by_slug, created, existing):
        for spec in ARTICLE_SPECS:
            defaults = {
                key: value for key, value in spec.items()
                if key not in {"slug", "series", "tags"}
            }
            defaults["series"] = series_by_slug[spec["series"]] if spec["series"] else None
            defaults["published"] = True
            post, is_created = Post.objects.get_or_create(slug=spec["slug"], defaults=defaults)
            if is_created:
                post.tags.add(*spec["tags"])
            self._report("article", spec["slug"], is_created, created, existing, "posts")

    def _seed_projects(self, created, existing):
        for spec in PROJECT_SPECS:
            defaults = {
                key: spec[key] for key in (
                    "title", "description", "date", "project_status", "project_featured",
                    "project_tech_stack",
                )
            }
            defaults.update({
                "category": "projects",
                "published": True,
                "content": _project_content(spec),
                "github_url": "",
                "demo_url": "",
                "paper_url": "",
                "series": None,
                "series_order": None,
            })
            project, is_created = Post.objects.get_or_create(slug=spec["slug"], defaults=defaults)
            if is_created:
                structured_updates = []
                for field_name, _heading in STRUCTURED_PROJECT_FIELDS:
                    if _model_has_field(Post, field_name):
                        desired = spec[field_name]
                        if getattr(project, field_name, "") != desired:
                            setattr(project, field_name, desired)
                            structured_updates.append(field_name)
                if structured_updates:
                    project.save(update_fields=structured_updates)
                project.tags.add(*spec["tags"])
            self._report("project", spec["slug"], is_created, created, existing, "posts")

    def _seed_media(self, created, existing, created_files, verbosity):
        project = Post.objects.filter(slug=MEDIA_PROJECT_SLUG, category="projects").first()
        if project is None:
            self.stdout.write(self.style.WARNING(
                f"Skipped demo media because {MEDIA_PROJECT_SLUG} is not an existing demo project."
            ))
            return

        for spec in MEDIA_SPECS:
            defaults = {
                "file": ContentFile(_render_demo_image(spec), name=spec["filename"]),
                "kind": ProjectMedia.KIND_IMAGE,
                "section": spec["section"],
                "description": spec["description"],
                "group_name": spec["group_name"],
                "order": spec["order"],
            }
            media, is_created = ProjectMedia.objects.get_or_create(
                project=project,
                title=spec["title"],
                defaults=defaults,
            )
            if is_created:
                created_files.append((media.file.storage, media.file.name))
            self._report("media", spec["title"], is_created, created, existing, "media")
            if verbosity >= 2:
                self.stdout.write(f"  {media.file.name} ({spec['width']} x {spec['height']})")

    def _dry_run(self):
        missing_series = [spec["slug"] for spec in SERIES_SPECS if not Series.objects.filter(slug=spec["slug"]).exists()]
        post_specs = [*ARTICLE_SPECS, *PROJECT_SPECS]
        missing_posts = [spec["slug"] for spec in post_specs if not Post.objects.filter(slug=spec["slug"]).exists()]
        project = Post.objects.filter(slug=MEDIA_PROJECT_SLUG, category="projects").first()
        missing_media = [
            spec["title"] for spec in MEDIA_SPECS
            if project is None or not ProjectMedia.objects.filter(project=project, title=spec["title"]).exists()
        ]

        self.stdout.write(self.style.WARNING("DRY RUN: no database or media writes will occur."))
        for label, values in (
            ("series", missing_series),
            ("posts", missing_posts),
            ("media", missing_media),
        ):
            self.stdout.write(f"Would create {len(values)} {label}:")
            for value in values:
                self.stdout.write(f"  - {value}")
        self.stdout.write(self.style.SUCCESS("DRY RUN complete; no objects or files were created."))
