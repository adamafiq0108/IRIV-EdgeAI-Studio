# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
about.py -- who made this product, for the About page.

  GET /api/hub/about    product, running version, company, website, copyright

Any signed-in account may read it: it is the same credit printed by
`hub_server.py --about`, and holds nothing about this box.
"""
from fastapi import APIRouter

from core import about

router = APIRouter()


@router.get("/api/hub/about", name="about.info")
def about_info():
    return {"ok": True, **about.info()}
