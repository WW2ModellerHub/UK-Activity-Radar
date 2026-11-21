# Office of Rail and Road statistics

Bundled as `app/assets/station_usage.json` and `app/assets/operator_performance.json`.
Open Government Licence v3.0. Refresh when ORR publishes (station usage yearly
in December; performance every four weeks):

    # Station usage, table 1415 (ODS) from
    # https://dataportal.orr.gov.uk/statistics/usage/estimates-of-station-usage/
    python3 scripts/orr/build_usage.py table-1415.ods app/assets/station_usage.json

    # Performance tables 3138 and 3124 as CSV (contentId 1681 and 1683):
    # https://dataportal.orr.gov.uk/umbraco/api/databricksreport/downloadcsv?contentId=1681
    python3 scripts/orr/build_perf.py 3138.csv 3124.csv app/assets/operator_performance.json
