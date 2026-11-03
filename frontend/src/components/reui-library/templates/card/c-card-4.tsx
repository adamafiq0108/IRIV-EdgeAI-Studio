import { Button } from "@/components/reui-library/ui/button"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/reui-library/ui/card"

export default function Pattern() {
  return (
    <Card className="w-full max-w-xs">
      <CardHeader className="border-b">
        <CardTitle>Header with Border</CardTitle>
      </CardHeader>
      <CardContent>
        <p>
          The footer has a border-t class applied, creating a visual separation
          between the content and footer sections.
        </p>
      </CardContent>
      <CardFooter className="border-t">
        <Button variant="outline" className="w-full">
          Action
        </Button>
      </CardFooter>
    </Card>
  )
}
