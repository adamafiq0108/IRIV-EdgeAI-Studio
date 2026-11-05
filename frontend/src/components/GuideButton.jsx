// Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
// GuideButton - "Guide", in the top bar next to the full-screen icon, on every page that has a
// guide. It opens the guide of the page being shown (workflow/guideState.js says which); on the
// Workflow, where the guide is a card in the right-hand column, it opens and lights up that card.
import { useLocation } from "react-router-dom";
import { BookOpen } from "lucide-react";
import { Button } from "./ui/button.jsx";
import { guideKeyForPath, requestGuide } from "../workflow/guideState.js";

export default function GuideButton() {
  const { pathname } = useLocation();
  const key = guideKeyForPath(pathname);
  if (!key) return null;
  return (
    <Button variant="ghost" size="sm" data-guide-button={key} onClick={() => requestGuide(key)}
            aria-label="Guide for this page" title="Show the guide for this page">
      <BookOpen /> Guide
    </Button>
  );
}
