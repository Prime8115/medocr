"""External-software connector subsystem.

Three connector types share one normalized, versioned push payload:
  - webhook       : signed HTTP POST to a shop URL (retry + backoff)
  - file_export   : CSV/JSON written to a folder and/or offered for download
  - desktop_agent : queued for a paired Windows companion app to collect
"""
