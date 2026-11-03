import { Field, FieldLabel } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"

export default function Pattern() {
  return (
    <Field className="w-full max-w-xs">
      <FieldLabel htmlFor="subtle-bg">Subtle Background</FieldLabel>
      <Input
        id="subtle-bg"
        className="bg-muted focus-visible:bg-muted hover:bg-muted transition-colors duration-300"
        placeholder="Enter text..."
      />
    </Field>
  )
}
