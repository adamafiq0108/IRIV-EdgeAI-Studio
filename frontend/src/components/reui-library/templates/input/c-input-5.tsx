import { Field, FieldError, FieldLabel } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"

export default function Pattern() {
  return (
    <Field className="max-w-xs">
      <FieldLabel htmlFor="input-demo-error">Email</FieldLabel>
      <Input
        id="input-demo-error"
        type="email"
        placeholder="name@example.com"
        aria-invalid="true"
      />
      <FieldError>Please enter a valid email address.</FieldError>
    </Field>
  )
}
