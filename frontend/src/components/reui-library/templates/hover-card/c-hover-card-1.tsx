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
          render={<Button variant="outline">Hover Me</Button>}
        />
        <HoverCardContent>
          <div className="flex flex-col gap-1">
            <h4 className="leading-none font-medium">Hover Card</h4>
            <p className="text-muted-foreground">
              A basic hover card that appears when you hover over the trigger.
            </p>
          </div>
        </HoverCardContent>
      </HoverCard>
    </div>
  )
}
