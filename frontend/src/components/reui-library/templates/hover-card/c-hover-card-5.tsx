import { Button } from "@/components/reui-library/ui/button"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/reui-library/ui/hover-card"

export default function Pattern() {
  return (
    <div className="flex min-h-[100px] items-center justify-center">
      <HoverCard>
        <HoverCardTrigger
          delay={100}
          closeDelay={100}
          render={<Button variant="outline">View Image</Button>}
        />
        <HoverCardContent className="w-[320px] overflow-hidden p-0">
          <img
            src="https://picsum.photos/1000/800?grayscale&random=1"
            alt="Card Image"
            className="aspect-video w-full object-cover"
          />
          <div className="space-y-1 p-3">
            <p className="font-medium">Image Overview</p>
            <p className="text-muted-foreground leading-relaxed">
              Visual overview of generated landing page.
            </p>
          </div>
        </HoverCardContent>
      </HoverCard>
    </div>
  )
}
