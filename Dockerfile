FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:ec9c0a0dc5f60adc2065762050638dd5ed5facfc53716d4580a42d86027c8e80 AS build
WORKDIR /src
COPY . .
RUN dotnet publish src/Sentinel.Api/Sentinel.Api.csproj -c Release -o /publish --no-self-contained

FROM mcr.microsoft.com/dotnet/aspnet:8.0@sha256:a3cd573ac05cf88ca496e3309cb7f7c44aa16664352d1ee41eda93154f50cdbf
WORKDIR /app
COPY --from=build /publish/ .
RUN mkdir /app/data && chown app:app /app/data
USER app
EXPOSE 8080
ENTRYPOINT ["dotnet", "Sentinel.Api.dll"]
