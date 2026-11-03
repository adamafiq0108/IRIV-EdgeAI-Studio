import {
  Frame,
  FrameHeader,
  FramePanel,
  FrameTitle,
} from "@/components/reui-library/reui/frame"

import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldTitle,
} from "@/components/reui-library/ui/field"
import { Separator } from "@/components/reui-library/ui/separator"
import { Switch } from "@/components/reui-library/ui/switch"

export default function Pattern() {
  return (
    <Frame className="w-full max-w-xs" spacing="sm">
      <FrameHeader>
        <FrameTitle>Privacy Settings</FrameTitle>
      </FrameHeader>
      <FramePanel className="overflow-hidden p-0!">
        <FieldGroup className="gap-0">
          <Field>
            <FieldLabel className="justify-between p-3">
              <FieldTitle>Profile visibility</FieldTitle>
              <Switch defaultChecked />
            </FieldLabel>
          </Field>
          <Separator />
          <Field>
            <FieldLabel className="justify-between p-3">
              <FieldTitle>Show online status</FieldTitle>
              <Switch defaultChecked />
            </FieldLabel>
          </Field>
          <Separator />
          <Field>
            <FieldLabel className="justify-between p-3">
              <FieldTitle>Allow data collection</FieldTitle>
              <Switch />
            </FieldLabel>
          </Field>
        </FieldGroup>
      </FramePanel>
    </Frame>
  )
}
