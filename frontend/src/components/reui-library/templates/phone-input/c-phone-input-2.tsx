import { PhoneInput } from "@/components/reui-library/reui/phone-input"

export default function Pattern() {
  return (
    <PhoneInput
      variant="sm"
      placeholder="Enter phone number"
      defaultCountry="NL"
      value="+31612345678"
    />
  )
}
