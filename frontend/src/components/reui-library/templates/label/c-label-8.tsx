import { Badge } from "@/components/reui-library/reui/badge"

import { Field } from "@/components/reui-library/ui/field"
import { Input } from "@/components/reui-library/ui/input"
import { Label } from "@/components/reui-library/ui/label"

export default function Pattern() {
  return (
    <Field className="w-full max-w-xs">
      <Label htmlFor="label-badge" className="gap-2">
        Webhook URL
        <Badge variant="success-light" size="sm">
          Active
        </Badge>
      </Label>
      <Input
        id="label-badge"
        type="url"
        defaultValue="https://api.example.com/webhooks"
        className="font-mono text-xs"
      />
    </Field>
  )
}
