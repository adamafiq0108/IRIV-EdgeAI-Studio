import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/reui-library/ui/input-group"
import { Kbd } from "@/components/reui-library/ui/kbd"

export default function Pattern() {
  return (
    <div className="flex items-center justify-center">
      <InputGroup className="max-w-xs">
        <InputGroupInput placeholder="Search…" />
        <InputGroupAddon>
          <Kbd>Space</Kbd>
        </InputGroupAddon>
      </InputGroup>
    </div>
  )
}
