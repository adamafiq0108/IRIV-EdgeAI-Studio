import { Field, FieldLabel } from "@/components/reui-library/ui/field"
import { Switch } from "@/components/reui-library/ui/switch"

export default function Pattern() {
  return (
    <div className="flex items-center justify-center">
      <Field orientation="horizontal">
        <Switch id="switch-basic" />
        <FieldLabel htmlFor="switch-basic">Airplane Mode</FieldLabel>
      </Field>
    </div>
  )
}
