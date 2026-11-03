import { Field } from "@/components/reui-library/ui/field"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/reui-library/ui/input-group"
import { IconPlaceholder } from "@/app/(create)/components/icon-placeholder"

export default function Pattern() {
  return (
    <Field className="max-w-xs">
      <InputGroup>
        <InputGroupAddon>
          <IconPlaceholder
            lucide="MicIcon"
            tabler="IconMicrophone"
            hugeicons="Mic02Icon"
            phosphor="MicrophoneIcon"
            remixicon="RiMicLine"
          />
        </InputGroupAddon>
        <InputGroupInput placeholder="Listening..." />
        <InputGroupAddon align="inline-end">
          <IconPlaceholder
            lucide="RadioIcon"
            tabler="IconBroadcast"
            hugeicons="LiveStreaming02Icon"
            phosphor="BroadcastIcon"
            remixicon="RiRfidLine"
            className="text-destructive animate-pulse"
          />
        </InputGroupAddon>
      </InputGroup>
    </Field>
  )
}
