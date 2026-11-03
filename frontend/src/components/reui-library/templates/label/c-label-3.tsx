import { Field } from "@/components/reui-library/ui/field"
import { Label } from "@/components/reui-library/ui/label"
import { Textarea } from "@/components/reui-library/ui/textarea"

export default function Pattern() {
  return (
    <Field className="w-full max-w-xs">
      <Label htmlFor="label-demo-message">Message</Label>
      <Textarea id="label-demo-message" placeholder="Type your message here…" />
    </Field>
  )
}
