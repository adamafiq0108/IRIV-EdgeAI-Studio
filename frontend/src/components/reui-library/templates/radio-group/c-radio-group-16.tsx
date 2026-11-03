import { Field, FieldLabel } from "@/components/reui-library/ui/field"
import {
  RadioGroup,
  RadioGroupItem,
} from "@/components/reui-library/ui/radio-group"

export default function Pattern() {
  return (
    <RadioGroup
      defaultValue="viewer"
      className="mx-auto flex w-auto flex-wrap items-center gap-6"
    >
      <Field orientation="horizontal" className="w-auto">
        <RadioGroupItem value="admin" id="role-admin" />
        <FieldLabel htmlFor="role-admin" className="cursor-pointer">
          Admin
        </FieldLabel>
      </Field>
      <Field orientation="horizontal" className="w-auto">
        <RadioGroupItem value="editor" id="role-editor" />
        <FieldLabel htmlFor="role-editor" className="cursor-pointer">
          Editor
        </FieldLabel>
      </Field>
      <Field orientation="horizontal" className="w-auto">
        <RadioGroupItem value="viewer" id="role-viewer" />
        <FieldLabel htmlFor="role-viewer" className="cursor-pointer">
          Viewer
        </FieldLabel>
      </Field>
    </RadioGroup>
  )
}
