resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"
  force_delete         = true # so `make destroy` leaves nothing behind
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
