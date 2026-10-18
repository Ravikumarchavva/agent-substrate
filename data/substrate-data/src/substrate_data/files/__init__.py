"""Governed file storage for volumes: versioned files, quotas and direct-to-storage transfers.

The control plane keeps metadata (paths, versions, quotas, audit); bytes move between clients and the
object store with presigned URLs. See docs/FILES.md.
"""
