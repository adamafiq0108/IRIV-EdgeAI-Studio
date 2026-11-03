import { Field } from "@/components/reui-library/ui/field"
import {
  InputGroup,
  InputGroupInput,
} from "@/components/reui-library/ui/input-group"

export default function Pattern() {
  return (
    <Field className="max-w-xs">
      <InputGroup>
        <InputGroupInput placeholder="Search..." />
      </InputGroup>
    </Field>
  )
}
