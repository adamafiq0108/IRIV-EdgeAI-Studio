import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/components/reui-library/ui/avatar"
import { Button } from "@/components/reui-library/ui/button"

export default function Pattern() {
  return (
    <Button>
      <Avatar className="size-5">
        <AvatarImage src="https://github.com/shadcn.png" alt="@shadcn" />
        <AvatarFallback>CH</AvatarFallback>
      </Avatar>
      <span className="text-xs">@shadcn</span>
    </Button>
  )
}
