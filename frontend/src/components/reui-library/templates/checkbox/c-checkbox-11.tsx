import { Card } from "@/components/reui-library/ui/card"
import { Checkbox } from "@/components/reui-library/ui/checkbox"
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/reui-library/ui/field"
import { Separator } from "@/components/reui-library/ui/separator"

export default function Pattern() {
  return (
    <Card className="w-full max-w-xs p-0">
      <FieldGroup className="gap-0">
        <Field>
          <FieldLabel className="px-4 py-3">
            <Checkbox defaultChecked />
            <FieldTitle>Push notifications</FieldTitle>
          </FieldLabel>
        </Field>
        <Separator />
        <Field>
          <FieldLabel className="px-4 py-3">
            <Checkbox />
            <FieldTitle>Email notifications</FieldTitle>
          </FieldLabel>
        </Field>
        <Separator />
        <Field>
          <FieldLabel className="px-4 py-3">
            <Checkbox />
            <FieldTitle>SMS notifications</FieldTitle>
          </FieldLabel>
        </Field>
      </FieldGroup>
    </Card>
  )
}
