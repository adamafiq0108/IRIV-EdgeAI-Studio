import { Button } from "@/components/reui-library/ui/button"
import { ButtonGroup } from "@/components/reui-library/ui/button-group"
import { Input } from "@/components/reui-library/ui/input"

export default function Pattern() {
  return (
    <ButtonGroup>
      <Button variant="outline">Button</Button>
      <Input placeholder="Type something here..." />
    </ButtonGroup>
  )
}
