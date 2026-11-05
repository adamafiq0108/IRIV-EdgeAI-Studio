# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
storage.py -- "This box no longer uses the NVMe drive" on Data Retention.

The NVMe-not-mounted alarm (core/services/storage_watch.py) remembers every
drive our data was seen on, and that memory had no way out of the product:
a box moved back to the eMMC on purpose alarmed on every page for ever.
This is the admin's way out: one confirmed POST drops the memory and
re-checks. What still expects the drive (an fstab line, a configured path)
is named in the answer, so the page can say why the banner stays instead
of leaving the admin to guess.

Site-admin, like the retention policy itself: it is a statement about the
operation's data, not about the hardware. Lives beside the retention routes
in spirit; in its own file because core/routes/hub.py is at its ceiling.
"""

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import provision as provision_service
from core.services import storage_watch

router = APIRouter()


@router.post("/api/hub/retention/nvme/forget", name="storage.forget_nvme")
async def forget_nvme(request: Request):
    st = request.app.state
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      st.config_dir):
        raise HTTPException(status_code=403,
                            detail="telling the box it no longer uses the NVMe "
                                   "drive is an admin action")
    pkg_dirs = [os.path.join(provision_service._pkg_root(st.systems_dir, s.slug), s.slug)
                for s in st.registry]
    out = await asyncio.to_thread(storage_watch.forget, st.config_dir, pkg_dirs)
    try:
        audit_service.record(action="retention.nvme_forget", result="ok",
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request),
                             detail={"forgotten": out["forgotten"],
                                     "still_expected": out["still_expected"]},
                             config_dir=st.config_dir)
    except Exception:                                        # noqa: BLE001
        pass                    # best-effort, as every box-setting route
    return {"ok": True, **out}
