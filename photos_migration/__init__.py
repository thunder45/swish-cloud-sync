"""Local GoPro Cloud -> Google Photos migration (no AWS).

Pipeline per unit (one downloadable file): download original from GoPro Cloud ->
compute true capture time/location -> optional adaptive HEVC re-encode -> write
metadata Google Photos reads -> upload once (SQLite ledger keyed on the
original's SHA-256).
"""
