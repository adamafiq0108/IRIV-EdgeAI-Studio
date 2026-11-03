# -*- coding: utf-8 -*-
"""Entry point: python -m core.db.migrations [--status]"""

import sys

from core.db.migrations import main

sys.exit(main())
