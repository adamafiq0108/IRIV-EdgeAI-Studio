import { Field, FieldError } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"
import { Label } from "@/components/reui-library/ui/label"

export default function Pattern() {
  return (
    <Field className="w-full max-w-xs" data-invalid="true">
      <Label htmlFor="label-error">Email</Label>
      <Input
        id="label-error"
        type="email"
        defaultValue="invalid-email"
        aria-invalid="true"
      />
      <FieldError>Please enter a valid email address</FieldError>
    </Field>
  )
}
