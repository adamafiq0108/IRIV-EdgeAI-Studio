import { Badge } from "@/components/reui-library/reui/badge"

import { Button } from "@/components/reui-library/ui/button"

export default function Pattern() {
  return (
    <Button aria-label="Updates (new)">
      Updates
      <Badge variant="success" size="xs" aria-hidden="true">
        New
      </Badge>
    </Button>
  )
}
