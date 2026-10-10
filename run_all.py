"""Cloud Run entry point: the weekly package scrapes, then the Tradera vs Blocket
private car census.

ONLY=private_cars_se runs the census alone (ops.yml -> run-private-cars-se), so it
can be tested without writing an extra package row to BigQuery.
"""
import os
import traceback

only = os.environ.get("ONLY", "").strip()

if only != "private_cars_se":
    import finn_mobility_packages_requests as finn
    import blocket_mobility_packages_requests as blocket

    print("=== Running finn mobility packages ===")
    finn.main()

    print("\n=== Running blocket mobility packages ===")
    blocket.main()

# The census is a separate measurement. A failure here must not fail the job
# after the package rows are already written, so it logs [ERR] and moves on.
print("\n=== Running Tradera vs Blocket private car census ===")
try:
    import private_cars_se_compare as pcs
    pcs.run(bigquery=True)
except Exception:
    print("[ERR] private_cars_se census failed:")
    traceback.print_exc()
    if only == "private_cars_se":
        raise
