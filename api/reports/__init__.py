"""
Reporting feature.

One report today - the collection report - served two ways from the same
snapshot: as JSON for the screen, and as a PDF for printing. Both go through
`services/collection_report.py`, which is the point: the number in the PDF and
the number on the screen are produced by the same code, so they cannot drift.

Where things live
-----------------
    periods.py      What window is being reported on, and what the window
                    immediately before it is. No database access.

    selectors.py    Every query. Takes filters, returns rows and aggregates.
                    Shapes nothing.

    services/       Assembly. `collection_report.py` turns selector output
                    into the response contract; `pdf.py` turns that same
                    snapshot into a PDF.

    serializers/    Query parameters in, and a written-out response schema so
                    the OpenAPI docs describe the payload rather than guessing.

    templates/      The PDF layout. Plain Django template - edit it freely,
                    nothing in the Python cares what it contains.

    views/          Two thin views. Parse, delegate, respond.
"""
