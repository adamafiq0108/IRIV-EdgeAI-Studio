import { Badge } from "@/components/reui-library/reui/badge"

import { Button } from "@/components/reui-library/ui/button"
import { ButtonGroup } from "@/components/reui-library/ui/button-group"
import { IconPlaceholder } from "@/app/(create)/components/icon-placeholder"

export default function Pattern() {
  return (
    <ButtonGroup>
      <Button variant="outline">
        <IconPlaceholder
          lucide="FileTextIcon"
          tabler="IconFileText"
          hugeicons="File02Icon"
          phosphor="FileTextIcon"
          remixicon="RiFileTextLine"
          aria-hidden="true"
        />
        <Badge variant="warning-light">Draft</Badge>
      </Button>

      <Button variant="outline">
        <IconPlaceholder
          lucide="PencilIcon"
          tabler="IconPencil"
          hugeicons="PenIcon"
          phosphor="PencilIcon"
          remixicon="RiPencilLine"
          aria-hidden="true"
        />
        <span>Edit</span>
      </Button>

      <Button variant="outline" size="icon">
        <IconPlaceholder
          lucide="UploadIcon"
          tabler="IconUpload"
          hugeicons="Upload01Icon"
          phosphor="UploadSimple"
          remixicon="RiUpload2Line"
          aria-hidden="true"
        />
      </Button>
    </ButtonGroup>
  )
}
