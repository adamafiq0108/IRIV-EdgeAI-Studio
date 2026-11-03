import { Checkbox } from "@/components/reui-library/ui/checkbox"
import { Field } from "@/components/reui-library/ui/field"
import { Label } from "@/components/reui-library/ui/label"

export default function Pattern() {
  return (
    <Field orientation="horizontal" className="mx-auto w-auto">
      <Checkbox id="label-demo-terms" />
      <Label htmlFor="label-demo-terms">Accept terms and conditions</Label>
    </Field>
  )
}
