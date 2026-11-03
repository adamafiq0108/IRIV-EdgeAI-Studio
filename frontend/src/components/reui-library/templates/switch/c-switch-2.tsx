import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
  FieldTitle,
} from "@/components/reui-library/ui/field"
import { Switch } from "@/components/reui-library/ui/switch"

export default function Pattern() {
  return (
    <div className="flex items-center justify-center">
      <FieldLabel htmlFor="switch-with-desc" className="w-full max-w-xs">
        <Field orientation="horizontal">
          <FieldContent>
            <FieldTitle>Share across devices</FieldTitle>
            <FieldDescription>
              Focus is shared across devices, and turns off when you leave the
              app.
            </FieldDescription>
          </FieldContent>
          <Switch id="switch-with-desc" />
        </Field>
      </FieldLabel>
    </div>
  )
}
