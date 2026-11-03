import { Button } from "@/components/reui-library/ui/button"
import { Spinner } from "@/components/reui-library/ui/spinner"

export default function Pattern() {
  return (
    <Button disabled>
      <Spinner aria-hidden="true" />
      Please wait
    </Button>
  )
}
