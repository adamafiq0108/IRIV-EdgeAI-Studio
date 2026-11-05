# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""Entry point: python -m core.db.migrations [--status]"""

import sys

from core.db.migrations import main

sys.exit(main())
