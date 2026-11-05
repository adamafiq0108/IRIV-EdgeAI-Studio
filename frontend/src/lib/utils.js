// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
